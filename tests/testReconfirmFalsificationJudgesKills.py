"""The re-confirmation harness credits a kill only when a test noticed.

A mutant is "killed" when the guarding test FAILS. Three other things
also make pytest exit nonzero without the test having noticed anything:
the mutation breaks collection, it breaks a fixture's setup, or it makes
the test hang. The harness used to credit every exit 1 as a kill, and it
never parsed a JavaScript mutant, so a typo in a ``.js`` mutation broke
the page for every browser test and read as a kill.

Each test below drives the real harness against a throwaway project in a
temporary directory, so the verdict is read from a real pytest run and a
real JUnit file, never from a stub that agrees with the code.
"""

import importlib.util
import os
import pathlib
import shutil
import threading
import time

import pytest

from tests.falsificationRegistry import Falsification


def _fmoduleLoadTool():
    """Load tools/reconfirmFalsification.py under a private name."""
    pathTool = (
        pathlib.Path(__file__).resolve().parent.parent
        / "tools" / "reconfirmFalsification.py"
    )
    spec = importlib.util.spec_from_file_location(
        "toolReconfirmJudgesKills", pathTool,
    )
    moduleTool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(moduleTool)
    return moduleTool


@pytest.fixture
def moduleToolInTemporaryTree(tmp_path, monkeypatch):
    """Return the tool with its mutation tree pointed at a scratch project."""
    moduleTool = _fmoduleLoadTool()
    monkeypatch.setattr(moduleTool, "PATH_TREE", tmp_path)
    return moduleTool


def _fnWriteProject(pathTree, dictFiles):
    """Write each ``name: text`` pair into the scratch project."""
    for sName, sText in dictFiles.items():
        (pathTree / sName).write_text(sText, encoding="utf-8")


def testATestThatFailsInItsCallPhaseIsAKill(
    moduleToolInTemporaryTree, tmp_path,
):
    _fnWriteProject(tmp_path, {
        "testGuard.py": "def testGuard():\n    assert 1 == 2\n",
    })
    sStatus = moduleToolInTemporaryTree._fsRunMutatedTestAndJudge(
        "testGuard.py::testGuard", 120.0,
    )
    assert sStatus == "KILLED", sStatus


def testATestThatPassesIsASurvivor(moduleToolInTemporaryTree, tmp_path):
    _fnWriteProject(tmp_path, {
        "testGuard.py": "def testGuard():\n    assert 1 == 1\n",
    })
    sStatus = moduleToolInTemporaryTree._fsRunMutatedTestAndJudge(
        "testGuard.py::testGuard", 120.0,
    )
    assert sStatus.startswith("SURVIVED"), sStatus


def testASetupErrorIsNotAKill(moduleToolInTemporaryTree, tmp_path):
    """A fixture that raises exits 1 exactly as a failing test does.

    Kills: crediting pytest exit 1 without reading the JUnit XML, which
    scored every mutation that broke a fixture as a defended guard.
    """
    _fnWriteProject(tmp_path, {
        "testGuard.py": (
            "import pytest\n\n\n"
            "@pytest.fixture\n"
            "def fixtureBroken():\n"
            "    raise RuntimeError('fixture setup broke')\n\n\n"
            "def testGuard(fixtureBroken):\n"
            "    assert True\n"
        ),
    })
    sStatus = moduleToolInTemporaryTree._fsRunMutatedTestAndJudge(
        "testGuard.py::testGuard", 120.0,
    )
    assert sStatus.startswith("ERROR: not a kill"), sStatus


def testACollectionErrorIsNotAKill(moduleToolInTemporaryTree, tmp_path):
    """A mutation that stops the test module importing defended nothing."""
    _fnWriteProject(tmp_path, {
        "testGuard.py": (
            "import moduleThatDoesNotExist\n\n\n"
            "def testGuard():\n    assert True\n"
        ),
    })
    sStatus = moduleToolInTemporaryTree._fsRunMutatedTestAndJudge(
        "testGuard.py::testGuard", 120.0,
    )
    assert sStatus.startswith("ERROR: not a kill"), sStatus


def testASetupErrorFromTheMutatedSourceIsNotCreditedEndToEnd(
    moduleToolInTemporaryTree, tmp_path,
):
    """The whole path: mutate a file, replay, restore it, refuse the credit."""
    _fnWriteProject(tmp_path, {
        "guarded.py": "def fiLimit():\n    return 1\n",
        "testGuard.py": (
            "import pytest\n\n\n"
            "@pytest.fixture\n"
            "def iLimit():\n"
            "    import guarded\n"
            "    assert guarded.fiLimit() == 1\n"
            "    return 1\n\n\n"
            "def testGuard(iLimit):\n"
            "    assert iLimit == 1\n"
        ),
    })
    entry = Falsification(
        nodeid="testGuard.py::testGuard", source="guarded.py",
        old="return 1", new="return 2",
    )
    sOriginal = (tmp_path / "guarded.py").read_text(encoding="utf-8")
    sStatus = moduleToolInTemporaryTree._fsReconfirmOne(
        entry, sOriginal, bPreconditionKnownGood=True,
        fTimeoutSeconds=120.0,
    )
    assert sStatus.startswith("ERROR: not a kill"), sStatus
    assert (tmp_path / "guarded.py").read_text(encoding="utf-8") == sOriginal


def testAHungMutatedTestIsKilledNamedAndLeavesNoOrphan(
    moduleToolInTemporaryTree, tmp_path,
):
    """One hanging mutant must not consume the shard that runs it.

    The synthetic mutant hangs AND starts a grandchild, as a real hub or
    browser would. Killing pytest alone leaves the grandchild holding
    the pipe open, so the wait would never return; the whole process
    group has to go.

    Kills: running a replayed entry without a wall-clock limit, which
    cost a CI shard 45 minutes.
    """
    sPidFile = str(tmp_path / "grandchild.pid")
    _fnWriteProject(tmp_path, {
        "testGuard.py": (
            "import subprocess\nimport time\n\n\n"
            "def testGuard():\n"
            "    processChild = subprocess.Popen(['sleep', '600'])\n"
            f"    open({sPidFile!r}, 'w').write(str(processChild.pid))\n"
            "    time.sleep(600)\n"
        ),
    })
    fStart = time.monotonic()
    sStatus = moduleToolInTemporaryTree._fsRunMutatedTestAndJudge(
        "testGuard.py::testGuard", 5.0,
    )
    assert time.monotonic() - fStart < 60, "the limit did not bite"
    assert sStatus.startswith("ERROR: timed out after 5s"), sStatus
    assert "testGuard.py::testGuard" in sStatus, "the entry must be named"
    iGrandchild = int(pathlib.Path(sPidFile).read_text())
    _fnAssertProcessGone(iGrandchild)


def _fnAssertProcessGone(iPid):
    """Fail unless the process has exited, allowing a moment for reaping."""
    for _ in range(50):
        try:
            os.kill(iPid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(iPid, 9)
    pytest.fail(f"the hung test's child {iPid} outlived the kill")


def testAJavaScriptMutantThatDoesNotParseIsNotAKill(
    moduleToolInTemporaryTree, tmp_path,
):
    """A typo in a .js mutant breaks the page, so every browser test fails.

    Kills: crediting a JavaScript mutant without parsing it.
    """
    if not moduleToolInTemporaryTree._fsFindNodeExecutable():
        pytest.skip("no node executable here to syntax-check with")
    (tmp_path / "page.js").write_text(
        "var iLimit = 1;\n", encoding="utf-8",
    )
    entry = Falsification(
        nodeid="testGuard.py::testGuard", source="page.js",
        old="var iLimit = 1;", new="var iLimit = ;",
    )
    sStatus = moduleToolInTemporaryTree._fsReconfirmOne(
        entry, "var iLimit = 1;\n", bPreconditionKnownGood=True,
        fTimeoutSeconds=120.0,
    )
    assert sStatus.startswith("ERROR: mutation does not parse"), sStatus
    assert (tmp_path / "page.js").read_text(encoding="utf-8") == (
        "var iLimit = 1;\n"
    )


def testValidJavaScriptPassesTheSyntaxCheck(moduleToolInTemporaryTree):
    if not moduleToolInTemporaryTree._fsFindNodeExecutable():
        pytest.skip("no node executable here to syntax-check with")
    assert moduleToolInTemporaryTree._fsDescribeJavaScriptSyntaxProblem(
        "(function () { var iLimit = 1; return iLimit; })();\n",
    ) == ""


def testEveryJavaScriptSourceInTheRegistryParsesBeforeMutation():
    """The syntax check must not reject a real, unmutated source.

    If a registry JS source is an ES module or otherwise fails
    ``node --check`` clean, every one of its entries would report
    "does not parse" and the new gate would misfire in CI.
    """
    moduleTool = _fmoduleLoadTool()
    if not moduleTool._fsFindNodeExecutable():
        pytest.skip("no node executable here to syntax-check with")
    pathRoot = pathlib.Path(__file__).resolve().parent.parent
    setSources = {
        entry.source for entry in moduleTool.LIST_FALSIFICATIONS
        if entry.source.endswith(".js")
    }
    assert setSources, "no JavaScript entries, so this proves nothing"
    dictProblems = {
        sSource: moduleTool._fsDescribeJavaScriptSyntaxProblem(
            (pathRoot / sSource).read_text(encoding="utf-8"),
        )
        for sSource in sorted(setSources)
    }
    assert not any(dictProblems.values()), dictProblems


def testEachResultIsPrintedBeforeTheNextEntryIsJudged(
    moduleToolInTemporaryTree, capsys, monkeypatch,
):
    """A job killed mid-run must leave a log of what it finished.

    Kills: collecting every result and printing at the end.
    """
    listSeenWhileJudging = []

    def fsJudgeAndRecordOutput(entry, sOriginal, **dictKeywords):
        listSeenWhileJudging.append(capsys.readouterr().out)
        return "KILLED"

    monkeypatch.setattr(
        moduleToolInTemporaryTree, "_fsReconfirmOne",
        fsJudgeAndRecordOutput,
    )
    listEntries = [
        Falsification(nodeid=f"t.py::t{i}", source="s.py", old="a", new="b")
        for i in range(2)
    ]
    moduleToolInTemporaryTree._flistReconfirmEntries(
        listEntries, {"s.py": "a"}, True, 1.0,
    )
    sRest = capsys.readouterr().out
    assert "t.py::t0" in listSeenWhileJudging[1], listSeenWhileJudging
    assert "t.py::t1" in sRest


def testAWorkersLinesAreEchoedWhileItIsStillRunning(
    moduleToolInTemporaryTree, capsys, tmp_path,
):
    """Worker output must stream; a captured-until-exit worker leaves no log.

    Kills: ``subprocess.run(..., capture_output=True)`` for the worker.
    """
    sReleaseFile = str(tmp_path / "release")
    sScript = (
        "import os, time\n"
        "print('first line', flush=True)\n"
        f"while not os.path.exists({sReleaseFile!r}):\n"
        "    time.sleep(0.05)\n"
        "print('second line', flush=True)\n"
    )
    import sys
    listResult = []
    threadWorker = threading.Thread(target=lambda: listResult.append(
        moduleToolInTemporaryTree._tRunOneWorker(
            1, [sys.executable, "-c", sScript],
        ),
    ))
    threadWorker.start()
    sSeenEarly = ""
    for _ in range(100):
        sSeenEarly += capsys.readouterr().out
        if "first line" in sSeenEarly:
            break
        time.sleep(0.1)
    pathlib.Path(sReleaseFile).write_text("go")
    threadWorker.join(timeout=60)
    assert "[worker 1] first line" in sSeenEarly, sSeenEarly
    assert listResult[0][0] == 0
    assert "second line" in listResult[0][1]


def testTheEntryTimeoutMustBePositive():
    import subprocess
    import sys
    pathTool = (
        pathlib.Path(__file__).resolve().parent.parent
        / "tools" / "reconfirmFalsification.py"
    )
    result = subprocess.run(
        [sys.executable, str(pathTool), "--entry-timeout", "0",
         "--completeness-only"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "positive" in result.stderr
    assert shutil is not None
