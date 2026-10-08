"""The container's copy of the marker contract is the dashboard's, not a lookalike.

The generated ``conftest.py`` cannot import vaibify, so the writer half
of ``testMarkerContract`` is transcribed into it with ``inspect``. Two
things can go wrong silently, and each has its own check here. A function
the transcription leaves out is a ``NameError`` inside a hook whose
failures are swallowed on purpose (a marker write never fails a session),
so the marker simply never appears. And a transcribed copy that has
drifted would write a marker the dashboard reads differently from how it
was meant. Both checks run the REAL generated source in a namespace that
has no vaibify in it.
"""

import builtins
import copy
import inspect
import symtable

import pytest

from vaibify.gui import conftestManager
from vaibify.gui import pipelineUtils
from vaibify.gui import testMarkerContract as contract

S_FAKE_REPO = "/workspace/proj-never-opened"


def _fdictExecuteGeneratedConftest():
    dictNamespace = {"__name__": "conftestUnderTest"}
    exec(
        compile(
            conftestManager.fsBuildConftestSource(S_FAKE_REPO),
            "<generatedConftest>", "exec"),
        dictNamespace)
    return dictNamespace


def _fsetGlobalNamesUsedBy(tableScope):
    setNames = {
        symbol.get_name() for symbol in tableScope.get_symbols()
        if symbol.is_global() and symbol.is_referenced()
    }
    for tableChild in tableScope.get_children():
        setNames |= _fsetGlobalNamesUsedBy(tableChild)
    return setNames


def _fsetGlobalNamesUsedByTranscribedSource():
    sSource = "\n\n".join(
        inspect.getsource(fnWriter)
        for fnWriter in contract.LIST_TRANSCRIBED_WRITER_FUNCTIONS)
    return _fsetGlobalNamesUsedBy(symtable.symtable(sSource, "<x>", "exec"))


def _fdictMarkerAfterTwoSessions(dictNamespace):
    fnMerge = dictNamespace["fdictMergeSessionIntoMarker"]
    dictOld = {
        "sDirectory": "StepDir", "sLabel": "A01", "iExitStatus": 0,
        "fTimestamp": 5.0, "sRunAtUtc": "2026-10-01T00:00:00Z",
        "dictCategories": {"integrity": {"iPassed": 3, "iFailed": 0}},
    }
    dictFirst = fnMerge(copy.deepcopy(dictOld), _fdictSession("r1", {
        "test_quantitative_a.py": _fdictWholeFile(
            "test_quantitative_a.py", {"test_x": "passed"})}))
    return fnMerge(dictFirst, _fdictSession("r2", {
        "test_integrity_a.py": _fdictWholeFile(
            "test_integrity_a.py", {"test_y": "failed"})}))


def _fdictSession(sRunId, dictFileResults):
    return {
        "sRunId": sRunId, "fTimestamp": 9.0, "sRunAtUtc": "2026-10-02T00:00:00Z",
        "iExitStatus": 0, "sDirectory": "StepDir", "sLabel": "A01",
        "dictOutputHashes": {"StepDir/out.dat": "a" * 40},
        "dictInputHashes": {},
        "listPresentFiles": [
            "test_quantitative_a.py", "test_integrity_a.py"],
        "dictFileResults": dictFileResults,
    }


def _fdictWholeFile(sFileName, dictOutcomes):
    return {
        "bWholeFile": True, "dictCollectionError": None,
        "dictOutcomes": {
            "tests/" + sFileName + "::" + sTest: sOutcome
            for sTest, sOutcome in dictOutcomes.items()},
        "listNodeIds": [
            "tests/" + sFileName + "::" + sTest for sTest in dictOutcomes],
    }


def test_every_transcribed_function_appears_in_the_conftest_verbatim():
    sConftest = conftestManager.fsBuildConftestSource(S_FAKE_REPO)
    for fnWriter in contract.LIST_TRANSCRIBED_WRITER_FUNCTIONS:
        assert inspect.getsource(fnWriter) in sConftest, (
            fnWriter.__name__ + " is listed for transcription but is not "
            "in the generated conftest as the dashboard's own source")
    assert inspect.getsource(pipelineUtils.fsBuildUniqueTemporaryPath) in (
        sConftest)


@pytest.mark.falsification
def test_the_transcribed_functions_need_nothing_the_conftest_lacks():
    """Kills: leaving a helper out of ``LIST_TRANSCRIBED_WRITER_FUNCTIONS``.

    A transcribed function that calls a helper nobody transcribed raises
    ``NameError`` in a container, where the hook swallows it.
    """
    dictNamespace = _fdictExecuteGeneratedConftest()
    setMissing = {
        sName for sName in _fsetGlobalNamesUsedByTranscribedSource()
        if sName not in dictNamespace and not hasattr(builtins, sName)
    }
    assert not setMissing, (
        "the transcribed contract uses names the generated conftest "
        "does not define: " + ", ".join(sorted(setMissing)))


@pytest.mark.falsification
def test_the_containers_merge_equals_the_dashboards_merge():
    """Kills: a transcribed copy that has drifted from ``testMarkerContract``."""
    dictContainer = _fdictMarkerAfterTwoSessions(
        _fdictExecuteGeneratedConftest())
    dictDashboard = _fdictMarkerAfterTwoSessions(
        {"fdictMergeSessionIntoMarker": contract.fdictMergeSessionIntoMarker})
    assert dictContainer == dictDashboard
    assert dictContainer["dictTestFiles"], (
        "the merge recorded nothing, so equal results prove nothing")


def test_the_conftests_category_prefixes_are_the_contracts():
    dictNamespace = _fdictExecuteGeneratedConftest()
    assert dictNamespace["T_CATEGORY_FILE_PREFIXES"] == (
        contract.T_CATEGORY_FILE_PREFIXES)
    for sFileName in ("test_integrity_a.py", "test_quantitative_a.py", "test_a.py"):
        assert dictNamespace["fsCategoryOfFileName"](sFileName) == (
            contract.fsCategoryOfFileName(sFileName))
